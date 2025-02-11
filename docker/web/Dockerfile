# Use the official Python 3.12 slim image as the base image.
FROM python:3.12-slim

# Set environment variables to optimize Python behavior inside the Docker container.
ENV PYTHONUNBUFFERED 1

# Copy the contents of the current directory (on the host) to the /app directory inside the container.
COPY . /app

# Set the working directory inside the container.
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

# Set the virtual environment in PATH **before installing anything**
ENV PATH="/py/bin:$PATH"

# Upgrade pip & setuptools
RUN pip install --upgrade pip setuptools

# Install Python dependencies
RUN pip install -r /app/requirements.txt

# Install Playwright **without browsers** (since you install them manually inside the container)
RUN playwright install --with-deps --dry-run

# Install Playwright dependencies
RUN playwright install-deps

# Create a non-root user for security
RUN adduser --disabled-password --home /app user

# Ensure the non-root user has necessary permissions
RUN chown -R user:user /app

# Set Playwright’s cache directory to an accessible location
ENV PLAYWRIGHT_BROWSERS_PATH=/app/playwright-browsers
RUN mkdir -p $PLAYWRIGHT_BROWSERS_PATH && chown -R user:user $PLAYWRIGHT_BROWSERS_PATH

# Switch to non-root user
USER user


