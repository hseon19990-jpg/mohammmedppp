FROM node:20-alpine

WORKDIR /app

# Copy only package.json — let npm resolve fresh (no lockfile conflicts)
COPY bot/package.json ./

# Install all dependencies
RUN npm install --no-package-lock

# Copy pre-compiled JS
COPY bot/dist ./dist

# Create the default data directory. On Railway, attach a Volume at /app/data
# so accounts survive deployments and container restarts.
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
