# Image for the Tenantly API and migrate binaries (Tenantly API source, cmd/api and cmd/migrate).
#
# Build with the Tenantly API checkout (sibling directory of this repo) as the build context:
#   docker build -f images/tenantly/Dockerfile -t tenantly-api:dev <path-to-tenantly-api-checkout>
#
# The final image is distroless, runs as nonroot, and contains /app/api and /app/migrate.

# ---- build ----
FROM golang:1.26-alpine AS build
WORKDIR /src

# Download modules first so this layer is cached until go.mod or go.sum changes.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/api ./cmd/api \
 && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/migrate ./cmd/migrate

# ---- final ----
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=build /out/* /app/
USER nonroot
EXPOSE 8080
ENTRYPOINT ["/app/api"]
