FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install ALL dependencies (including dev for build)
RUN npm install

# Copy source code
COPY . .

# Build TypeScript → dist/
RUN npm run build

# Remove dev dependencies after build
RUN npm prune --production

# Create data directory
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
