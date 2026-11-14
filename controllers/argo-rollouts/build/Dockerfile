FROM gcr.io/distroless/static-debian12

COPY --chmod=0755 bin/rollouts-controller /bin/rollouts-controller

USER 999
WORKDIR /home/argo-rollouts

ENTRYPOINT ["/bin/rollouts-controller"]
