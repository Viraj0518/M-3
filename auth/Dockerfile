# The auth service. Node, because Better Auth is a TypeScript library — the
# bridge stays pure Python and talks to this over a JWKS URL.
FROM node:22-alpine

WORKDIR /srv/auth

# Dependencies first, so a source edit does not re-resolve the tree.
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY tsconfig.json ./
COPY src ./src
COPY scripts ./scripts

# 8932 sits beside the bridge's 8931.
EXPOSE 8932
ENV AUTH_HOST=0.0.0.0 AUTH_PORT=8932

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD wget -qO- http://127.0.0.1:8932/health >/dev/null || exit 1

CMD ["npx", "tsx", "src/server.ts"]
