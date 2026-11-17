FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS certificates

RUN apk --no-cache add 'ca-certificates>=20261020-r0'

FROM scratch

WORKDIR /

COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --chmod=0755 bin/manager_amd64 /manager

USER 65532:65532

ENTRYPOINT ["/manager"]
