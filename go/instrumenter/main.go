package main

import (
	"fmt"
	"log"
	"os"
	"path"
)

func instrumentKubernetesForTest(k8s_filepath string) {
	watchCacheGoFile := path.Join(k8s_filepath, "staging", "src", "k8s.io", "apiserver", "pkg", "storage", "cacher", "watch_cache.go")
	fmt.Printf("instrumenting %s\n", watchCacheGoFile)
	instrumentWatchCacheGoForAll(watchCacheGoFile, watchCacheGoFile, "Test", true, true)
}

func instrumentKubernetesForLearn(k8s_filepath string) {
	watchCacheGoFile := path.Join(k8s_filepath, "staging", "src", "k8s.io", "apiserver", "pkg", "storage", "cacher", "watch_cache.go")
	fmt.Printf("instrumenting %s\n", watchCacheGoFile)
	instrumentWatchCacheGoForAll(watchCacheGoFile, watchCacheGoFile, "Learn", true, false)
}

func instrumentControllerForLearn(config InstrumentationConfig) {
	applicationFilePath := config.ApplicationPath
	clientGoPath := config.ClientGoPath
	reconcileFunctions := config.ReconcileFunctions
	apis := config.APIs

	sharedInformerGoFile := path.Join(clientGoPath, "tools", "cache", "shared_informer.go")
	fmt.Printf("instrumenting %s\n", sharedInformerGoFile)
	instrumentSharedInformerGoForAll(sharedInformerGoFile, sharedInformerGoFile, "Learn")

	requestGoFile := path.Join(clientGoPath, "rest", "request.go")
	fmt.Printf("instrumenting %s\n", requestGoFile)
	instrumentRequestGoForAll(requestGoFile, requestGoFile, "Learn")

	storeGoFile := path.Join(clientGoPath, "tools", "cache", "store.go")
	fmt.Printf("instrumenting %s\n", storeGoFile)
	instrumentStoreGoForAll(storeGoFile, storeGoFile, "Learn")

	for filePath, stackFrame := range reconcileFunctions {
		source_file_to_instrument := path.Join(applicationFilePath, filePath)
		pkg, recvType, funName, err := reconcileTarget(stackFrame)
		if err != nil {
			panic(err)
		}
		fmt.Printf("instrumenting %s\n", source_file_to_instrument)
		instrumentAnnotatedReconcile(source_file_to_instrument, source_file_to_instrument, pkg, funName, recvType, stackFrame)
	}

	for _, api_to_instrument := range apis {
		module, filePath, pkg, funName, recvType := api_to_instrument.Module, api_to_instrument.FilePath, api_to_instrument.Package, api_to_instrument.Function, api_to_instrument.ReceiverType
		customizedImportMap := api_to_instrument.ImportMap
		source_file_to_instrument := path.Join(applicationFilePath, "sieve-dependency", "src", module, filePath)
		fmt.Printf("instrumenting %s\n", source_file_to_instrument)
		instrumentAnnotatedAPI(source_file_to_instrument, source_file_to_instrument, module, filePath, pkg, funName, recvType, "Learn", customizedImportMap, true)
	}
}

func instrumentControllerForTest(config InstrumentationConfig) {
	clientGoPath := config.ClientGoPath
	applicationFilePath := config.ApplicationPath
	apis := config.APIs

	requestGoFile := path.Join(clientGoPath, "rest", "request.go")
	fmt.Printf("instrumenting %s\n", requestGoFile)
	instrumentRequestGoForAll(requestGoFile, requestGoFile, "Test")

	sharedInformerGoFile := path.Join(clientGoPath, "tools", "cache", "shared_informer.go")
	fmt.Printf("instrumenting %s\n", sharedInformerGoFile)
	instrumentSharedInformerGoForAll(sharedInformerGoFile, sharedInformerGoFile, "Test")

	storeGoFile := path.Join(clientGoPath, "tools", "cache", "store.go")
	fmt.Printf("instrumenting %s\n", storeGoFile)
	instrumentStoreGoForAll(storeGoFile, storeGoFile, "Test")

	for _, api_to_instrument := range apis {
		module, filePath, pkg, funName, recvType := api_to_instrument.Module, api_to_instrument.FilePath, api_to_instrument.Package, api_to_instrument.Function, api_to_instrument.ReceiverType
		customizedImportMap := api_to_instrument.ImportMap
		source_file_to_instrument := path.Join(applicationFilePath, "sieve-dependency", "src", module, filePath)
		fmt.Printf("instrumenting %s\n", source_file_to_instrument)
		instrumentAnnotatedAPI(source_file_to_instrument, source_file_to_instrument, module, filePath, pkg, funName, recvType, "Test", customizedImportMap, true)
	}
}

func main() {
	if len(os.Args) != 2 {
		log.Fatal("usage: sieve-instrumenter config.json")
	}
	config, err := readConfig(os.Args[1])
	if err != nil {
		log.Fatal(err)
	}
	if config.Mode == VANILLA {
		return
	}
	if config.Project == "kubernetes" {
		if config.Mode == LEARN {
			instrumentKubernetesForLearn(config.KubernetesPath)
		} else {
			instrumentKubernetesForTest(config.KubernetesPath)
		}
	} else {
		if config.Mode == LEARN {
			instrumentControllerForLearn(config)
		} else {
			instrumentControllerForTest(config)
		}
	}
}
