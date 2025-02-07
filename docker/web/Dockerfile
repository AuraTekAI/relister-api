# Use the official Python 3.12 slim image as the base image.
FROM python:3.12-slim

# Set environment variables to optimize Python behavior inside the Docker container.
ENV PYTHONDOWNWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Copy the contents of the current directory (on the host) to the /app directory inside the container.
COPY . /app

# Set the working directory inside the container to /app.
WORKDIR /app

# Install system dependencies required for Playwright and PostgreSQL
RUN apt-get update && apt-get install -y \
    curl wget unzip \
    libnss3 libatk1.0-0 libx11-xcb1 libxcb-dri3-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpangocairo-1.0-0 \
    libpango-1.0-0 libcairo2 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment
RUN python -m venv /py

# Upgrade pip & setuptools
RUN /py/bin/pip install --upgrade pip setuptools

# Install Python dependencies
RUN /py/bin/pip install -r /app/requirements.txt

# Install Playwright and its browsers BEFORE switching to a non-root user
RUN /py/bin/playwright install --with-deps

# Create a non-root user for security
RUN adduser --disabled-password --no-create-home user

# Set the virtual environment in PATH
ENV PATH="/py/bin:$PATH"

# Switch to non-root user
USER user
