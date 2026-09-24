# The backup tools (docs/operations_design.md, O7): pg_dump and pg_restore for PostgreSQL 17,
# age for encryption, and the AWS CLI for S3-compatible storage. No MintFlow code is inside.
FROM alpine:3.22

RUN apk add --no-cache bash age aws-cli postgresql17-client \
    && adduser -D -H -u 10001 -s /sbin/nologin backup \
    && mkdir /work && chown backup /work

COPY backup restore /usr/local/bin/
USER backup
WORKDIR /work
