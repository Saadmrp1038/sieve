FROM gcr.io/distroless/static:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3

COPY --chmod=0755 keda /keda

USER 65532:65532

LABEL org.opencontainers.image.source="https://github.com/kedacore/keda" \
      org.opencontainers.image.version="2.21.0" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/keda"]
