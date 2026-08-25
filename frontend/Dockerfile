FROM node:22-alpine AS deps
WORKDIR /app
COPY package*.json ./
# SEC-1: npm ci (no `npm install`) — instala EXACTAMENTE el lockfile
# commiteado, sin resolver versiones nuevas en build. Tras el incidente de
# agosto-2026 el árbol de dependencias es parte de la superficie de ataque.
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# SEC-1: Next.js corre como usuario SIN privilegios (patrón oficial de la
# imagen standalone). En el incidente de agosto-2026 el minero corrió como
# root dentro del contenedor y pudo escribir /etc/ld.so.preload, crontabs y
# passwd; con este usuario ese daño no es posible.
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
