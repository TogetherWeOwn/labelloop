FROM docker.io/library/alpine:3.23.3@sha256:25109184c71bdad752c8312a8623239686a9a2071e8825f20acb8f2198c3f659
ARG TARGETARCH
ARG MINIO_SHA256_AMD64=7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f
ARG MINIO_SHA256_ARM64=5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d
RUN case "$TARGETARCH" in \
      amd64) checksum="$MINIO_SHA256_AMD64" ;; \
      arm64) checksum="$MINIO_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    for attempt in 1 2 3; do \
      if wget --no-verbose -T 30 "https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z" -O /usr/local/bin/minio; then break; fi; \
      test "$attempt" -lt 3 || exit 1; \
    done && \
    echo "$checksum /usr/local/bin/minio" | sha256sum -c - && \
    chmod 0555 /usr/local/bin/minio
EXPOSE 9000 9001
CMD ["minio", "server", "/data", "--console-address", ":9001"]
