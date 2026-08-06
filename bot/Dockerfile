FROM node:20-alpine

WORKDIR /app

# Copy only package.json — let npm resolve fresh (no lockfile conflicts)
COPY bot/package.json ./

# Install all dependencies
RUN npm install --no-package-lock

# Copy pre-compiled JS
COPY bot/dist ./dist

# Create data directory
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
