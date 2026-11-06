FROM quay.io/prometheus/busybox-linux-amd64@sha256:ea38411e568ddef8ddf8ee0765697b9d33182c0ff07e6a84a80180bd35fc4f22

COPY --chmod=0755 operator /bin/operator

USER 65534

LABEL org.opencontainers.image.source="https://github.com/prometheus-operator/prometheus-operator" \
      org.opencontainers.image.version="0.94.2" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/bin/operator"]
