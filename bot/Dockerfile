FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package.json ./

# Install dependencies
RUN npm install --omit=dev --ignore-scripts

# Install tsx for build step
RUN npm install tsx typescript @types/node @types/nodemailer --ignore-scripts

# Copy source
COPY . .

# Build TypeScript
RUN npx tsc

# Remove dev dependencies after build
RUN npm prune --omit=dev

# Create data directory for encrypted storage
RUN mkdir -p /app/data

EXPOSE 3000

CMD ["node", "dist/index.js"]
