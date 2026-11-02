package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/rpc"
	"time"
)

// The host workload invokes this public command inside its own Kind node.
// There is deliberately no command to release a held invocation manually.
func overlapCommand(args []string, output io.Writer) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: sieve-coordinator overlap status|held|completed --timeout-seconds N")
	}
	wait := args[0]
	if wait != "status" && wait != "held" && wait != "completed" {
		return fmt.Errorf("invalid overlap wait condition")
	}
	flags := flag.NewFlagSet("overlap", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	seconds := flags.Int("timeout-seconds", 20, "bounded wait")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 || *seconds < 1 || *seconds > 60 {
		return fmt.Errorf("overlap timeout must be 1–60 seconds")
	}
	connection, err := net.DialTimeout("tcp", "127.0.0.1:12345", 3*time.Second)
	if err != nil {
		return err
	}
	defer connection.Close()
	if err := connection.SetDeadline(time.Now().Add(time.Duration(*seconds+1) * time.Second)); err != nil {
		return err
	}
	client := rpc.NewClient(connection)
	defer client.Close()
	var status DeliveryOverlapStatus
	if err := client.Call("TestCoordinator.DeliveryOverlap", DeliveryOverlapRequest{WaitFor: wait, TimeoutMillis: *seconds * 1000}, &status); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(status)
}
