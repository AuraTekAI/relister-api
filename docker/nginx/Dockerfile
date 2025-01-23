# Use the official Nginx image based on Alpine Linux as the base image.
# The Alpine version is a minimal Docker image, which helps in keeping the image size small and lightweight.
FROM nginx:alpine

# Copy the custom Nginx configuration file from the local directory on the host
# to the appropriate location inside the container.
# This configuration file will override the default Nginx configuration.
COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf

# Expose port 80 to the host machine so that the Nginx server can be accessed
# externally on this port. Port 80 is the default HTTP port.
EXPOSE 80
