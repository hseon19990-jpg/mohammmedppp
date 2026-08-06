FROM node:20-alpine

WORKDIR /app

# Copy bot package files (build context = repo root)
COPY bot/package.json bot/package-lock.json* ./

# Install all dependencies
RUN npm install

# Copy bot source
COPY bot/src ./src
COPY bot/tsconfig.json ./tsconfig.json

# Create data directory
RUN mkdir -p /app/data

EXPOSE 3000

# Run TypeScript directly with tsx (no compile step needed)
CMD ["node_modules/.bin/tsx", "src/index.ts"]
