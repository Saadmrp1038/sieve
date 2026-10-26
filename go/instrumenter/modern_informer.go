package main

import (
	"fmt"
	"go/token"

	"github.com/dave/dst"
	"github.com/dave/dst/decorator"
	"github.com/dave/dst/decorator/resolver/goast"
	"github.com/dave/dst/decorator/resolver/guess"
)

func sieveStatements(source string) []dst.Stmt {
	parser := decorator.NewDecoratorWithImports(token.NewFileSet(), "cache", goast.WithResolver(guess.WithMap(auxiliaryImportMap())))
	file, err := parser.Parse("package cache\nimport sieve \"sieve.client\"\nfunc injected() {\n" + source + "\n}")
	check(err)
	return file.Decls[1].(*dst.FuncDecl).Body.List
}

func modernFunction(file *dst.File, name string) *dst.FuncDecl {
	for _, declaration := range file.Decls {
		if function, ok := declaration.(*dst.FuncDecl); ok && function.Recv == nil && function.Name.Name == name {
			return function
		}
	}
	panic("unsupported client-go informer: missing " + name)
}

func deltaLoop(function *dst.FuncDecl) *dst.RangeStmt {
	for _, statement := range function.Body.List {
		if loop, ok := statement.(*dst.RangeStmt); ok {
			if source, ok := loop.X.(*dst.Ident); ok && source.Name == "deltas" {
				if value, ok := loop.Value.(*dst.Ident); ok && value.Name == "d" {
					return loop
				}
			}
		}
	}
	panic("unsupported client-go informer: missing delta loop in " + function.Name.Name)
}

func beforeDelta(mode, operation, object string) []dst.Stmt {
	return sieveStatements(fmt.Sprintf(`
        sieveRecvID := -1
        _, sieveShared := handler.(*sharedIndexInformer)
        if sieveShared {
            sieveRecvID = sieve.Notify%sBeforeControllerRecv(%s, %s)
        }
    `, mode, operation, object))
}

func afterDelta(mode, id, operation, object string) []dst.Stmt {
	return sieveStatements(fmt.Sprintf(`
        if sieveShared {
            sieve.Notify%sAfterControllerRecv(%s, %s, %s)
        }
    `, mode, id, operation, object))
}

func instrumentModernInformer(input, output, mode string) {
	file := parseSourceFile(input, "cache", nil)
	ordinary := deltaLoop(modernFunction(file, "processDeltas"))
	ordinary.Body.List = append(beforeDelta(mode, "string(d.Type)", "d.Object"), ordinary.Body.List...)
	ordinary.Body.List = append(ordinary.Body.List, afterDelta(mode, "sieveRecvID", "string(d.Type)", "d.Object")...)
	// SyncAll contains individual notifications without modifying the store.
	dst.Inspect(ordinary, func(node dst.Node) bool {
		if loop, ok := node.(*dst.RangeStmt); ok {
			if source, ok := loop.X.(*dst.Ident); ok && source.Name == "objs" {
				loop.Body.List = append(beforeDelta(mode, `"Sync"`, "obj"), loop.Body.List...)
				loop.Body.List = append(loop.Body.List, afterDelta(mode, "sieveRecvID", `"Sync"`, "obj")...)
				return false
			}
		}
		return true
	})

	batch := deltaLoop(modernFunction(file, "processDeltasInBatch"))
	batch.Body.List = append(beforeDelta(mode, "string(d.Type)", "d.Object"), batch.Body.List...)
	callbacks := 0
	dst.Inspect(batch, func(node dst.Node) bool {
		if callback, ok := node.(*dst.FuncLit); ok {
			callback.Body.List = append(callback.Body.List, afterDelta(mode, "sieveRecvID", "string(d.Type)", "d.Object")...)
			callbacks++
			return false
		}
		return true
	})
	if callbacks != 3 {
		panic("unsupported client-go informer: expected add/update/delete batch callbacks")
	}
	// Keep the original transaction intact: all starts precede the atomic cache
	// update, and only callbacks for successful operations complete receipts.
	instrumentReplacementReceipts(modernFunction(file, "processReplacedAllInfo"), mode)
	writeInstrumentedFile(output, "cache", file, nil)
}

func instrumentReplacementReceipts(function *dst.FuncDecl, mode string) {
	function.Body.List = append(sieveStatements(`
        _, sieveShared := handler.(*sharedIndexInformer)
        var sieveDeletedReceipts, sieveReplacedReceipts []int
    `), function.Body.List...)
	found := false
	dst.Inspect(function, func(node dst.Node) bool {
		call, ok := node.(*dst.CallExpr)
		if !ok {
			return true
		}
		name, ok := call.Fun.(*dst.Ident)
		if !ok || name.Name != "reconcileReplacement" {
			return true
		}
		if len(call.Args) != 7 {
			panic("unsupported reconcileReplacement arguments")
		}
		for _, target := range []struct {
			index                       int
			operation, object, receipts string
		}{
			{5, `"Deleted"`, "obj.Obj", "sieveDeletedReceipts"},
			{6, `"Replaced"`, "obj", "sieveReplacedReceipts"},
		} {
			callback, ok := call.Args[target.index].(*dst.FuncLit)
			if !ok {
				panic("unsupported reconcileReplacement callback")
			}
			statements := beforeDelta(mode, target.operation, target.object)
			statements = append(statements, sieveStatements(fmt.Sprintf("%s = append(%s, sieveRecvID)", target.receipts, target.receipts))...)
			callback.Body.List = append(statements, callback.Body.List...)
		}
		found = true
		return false
	})
	if !found {
		panic("unsupported client-go informer: missing replacement collection")
	}
	loops := 0
	for _, statement := range function.Body.List {
		loop, ok := statement.(*dst.RangeStmt)
		if !ok {
			continue
		}
		source, ok := loop.X.(*dst.Ident)
		if !ok {
			continue
		}
		var operation, object, receipts string
		switch source.Name {
		case "deletions":
			operation, object, receipts = `"Deleted"`, "objToDelete.Obj", "sieveDeletedReceipts"
		case "replacements":
			operation, object, receipts = `"Replaced"`, "r.newObj", "sieveReplacedReceipts"
		default:
			continue
		}
		loop.Key = dst.NewIdent("sieveIndex")
		loop.Body.List = append(loop.Body.List, afterDelta(mode, receipts+"[sieveIndex]", operation, object)...)
		loops++
	}
	if loops != 2 {
		panic("unsupported client-go informer: missing replacement notifications")
	}
}
