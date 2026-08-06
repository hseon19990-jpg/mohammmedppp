FROM node:20-alpine

WORKDIR /app

# Copy package files (build context = repo root)
COPY bot/package.json bot/package-lock.json* ./

# Install production dependencies only
RUN npm install

# Copy pre-compiled JS (no compile step needed)
COPY bot/dist ./dist

# Create data directory
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
