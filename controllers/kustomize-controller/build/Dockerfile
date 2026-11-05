FROM alpine:3.24
RUN apk --no-cache add ca-certificates tini git openssh-client gnupg \
  && update-ca-certificates
COPY bin/kustomize-controller /usr/local/bin/
USER 65534:65534
ENV GNUPGHOME=/tmp
ENTRYPOINT ["/sbin/tini", "--", "kustomize-controller"]
