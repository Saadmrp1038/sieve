package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/rpc"
	"os"
	"os/signal"
	"syscall"

	sieve "sieve.client"
)

func serve(ctx context.Context, phase string) error {
	server := rpc.NewServer()
	var actionErrors <-chan error
	switch phase {
	case sieve.LEARN:
		if err := server.Register(NewLearnListener()); err != nil {
			return err
		}
	case sieve.TEST:
		listener, err := NewTestCoordinator(ctx)
		if err != nil {
			return err
		}
		actionErrors = listener.Server.stateMachine.errors
		if err := server.Register(listener); err != nil {
			return err
		}
	default:
		return fmt.Errorf("unrecognized mode %q", phase)
	}
	inbound, err := net.Listen("tcp", ":12345")
	if err != nil {
		return err
	}
	defer inbound.Close()
	go server.Accept(inbound)
	select {
	case <-ctx.Done():
		return nil
	case err := <-actionErrors:
		return fmt.Errorf("Sieve action failed: %w", err)
	}
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile | log.Lmicroseconds)
	if len(os.Args) != 2 {
		log.Fatal("usage: sieve-coordinator learn|test")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := serve(ctx, os.Args[1]); err != nil {
		log.Fatal(err)
	}
}
