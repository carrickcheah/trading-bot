# Multi-stage: build React with Bun, then run Hono on Bun
FROM oven/bun:1.3.13 AS build
WORKDIR /app
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile || bun install
COPY tsconfig.json server.ts ./
COPY src ./src
COPY public ./public
RUN bun run build

FROM oven/bun:1.3.13 AS runtime
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/public ./public
COPY --from=build /app/server.ts ./
COPY --from=build /app/package.json ./

ENV NODE_ENV=production
ENV PORT=3001
ENV API_URL=http://trading-bot-py:8002
EXPOSE 3001

CMD ["bun", "run", "server.ts"]
