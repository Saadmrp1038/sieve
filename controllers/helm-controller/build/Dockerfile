FROM alpine:3.24

RUN apk add --no-cache ca-certificates \
  && update-ca-certificates

COPY bin/helm-controller /usr/local/bin/

USER 65534:65534

ENTRYPOINT ["helm-controller"]
