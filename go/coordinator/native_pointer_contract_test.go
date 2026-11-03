package main

import (
	"fmt"
	"testing"
)

func TestDeliveryOverlapAcceptsExactNativePointerReceiverNames(t *testing.T) {
	for _, frame := range []string{
		"github.com/external-secrets/external-secrets/pkg/controllers/externalsecret.(*Reconciler).Reconcile",
		"github.com/cert-manager/cert-manager/pkg/controller/certificates/trigger.(*controller).ProcessItem",
	} {
		t.Run(frame, func(t *testing.T) {
			client, _ := coverageContract(t, fmt.Sprintf(`coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: observe
  resourceKey: externalsecret/default/a
  reconciler: %q
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`, frame))
			var status overlapReply
			if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"status", 1000}, &status); err != nil {
				t.Fatal(err)
			}
			if status.Reconciler != frame {
				t.Fatalf("lost exact frame: %q", status.Reconciler)
			}
		})
	}
}
