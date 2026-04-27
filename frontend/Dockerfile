FROM node:20-alpine

WORKDIR /app

# Install dependencies first for better caching
COPY package*.json ./
RUN npm install

# Copy the rest of the source code
COPY . .

# Vite dev server port
EXPOSE 5173

# Run vite with --host to allow external access (essential for Docker)
CMD ["npm", "run", "dev", "--", "--host"]
