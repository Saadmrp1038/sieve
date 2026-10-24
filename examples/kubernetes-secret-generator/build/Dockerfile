ARG SIEVE_BUILD_PLATFORM=linux/amd64
FROM --platform=${SIEVE_BUILD_PLATFORM} golang:1.25.0-bookworm AS builder
WORKDIR /workdir
COPY go.mod go.sum ./
COPY sieve-dependency/ sieve-dependency/
COPY cmd/ cmd/
COPY pkg/ pkg/
COPY version/ version/
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local go build -mod=mod -p 2 -o /manager ./cmd/manager

FROM scratch
COPY --from=builder /manager /manager
USER 65532:65532
ENTRYPOINT ["/manager"]
