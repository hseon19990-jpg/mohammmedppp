FROM node:20-alpine

WORKDIR /app

# Copy bot's package files explicitly (build context = repo root)
COPY bot/package.json bot/package-lock.json* ./

# Install all dependencies (dev + prod for build step)
RUN npm install

# Copy bot source code only
COPY bot/src ./src
COPY bot/tsconfig.json ./tsconfig.json

# Build TypeScript → dist/
RUN npm run build

# Remove dev dependencies after build
RUN npm prune --production

# Create data directory
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
