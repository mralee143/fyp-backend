# Frontend image — Next.js 16 compiled to a standalone server.
#
#   docker compose build frontend
#
# NEXT_PUBLIC_API_URL is inlined into the client bundle at BUILD time, so it has
# to be the URL the *browser* uses, not the compose service name. It is a build
# arg rather than a runtime env var for that reason: changing it means a rebuild
# (`docker compose build frontend`), not just a restart.

# --------------------------------------------------------------------------- #
# Stage 1 — dependencies
# --------------------------------------------------------------------------- #
FROM node:22-alpine AS deps

WORKDIR /app
COPY package.json package-lock.json ./
# Dev deps included: TypeScript and Tailwind are needed to build.
RUN npm ci

# --------------------------------------------------------------------------- #
# Stage 2 — build
# --------------------------------------------------------------------------- #
FROM node:22-alpine AS builder

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# --------------------------------------------------------------------------- #
# Stage 3 — runtime
# --------------------------------------------------------------------------- #
FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

# `output: "standalone"` in next.config.ts emits a self-contained server plus
# only the node_modules it actually reached — a few MB instead of ~500.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

# Probes $PORT, not a fixed 3000: a PaaS (Coolify, Railway, Fly) injects its own
# PORT, `server.js` binds that, and a hardcoded 3000 would then fail forever and
# mark a perfectly healthy container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "const p=process.env.PORT||3000;fetch('http://127.0.0.1:'+p+'/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
