# Use the official Python 3.12 slim image as the base image.
# The slim version is a lightweight image, reducing the overall size of the container.
FROM python:3.12-slim

# Set environment variables to optimize Python behavior inside the Docker container.
# PYTHONDOWNWRITEBYTECODE=1 prevents Python from writing .pyc files, which can save space.
# PYTHONUNBUFFERED=1 ensures that Python output is sent straight to the terminal (stdout/stderr) without being buffered, allowing for real-time logging.
ENV PYTHONDOWNWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Copy the contents of the current directory (on the host) to the /app directory inside the container.
# This typically includes your application code and dependencies.
COPY . /app

# Set the working directory inside the container to /app.
# All subsequent commands will be run from this directory.
WORKDIR /app

# Expose port 8000 to the host.
# This is the port where the Python web application (Django) will listen for incoming requests.
EXPOSE 8000

# Create a virtual environment in the /py directory.
# Update package lists for APT.
# Install necessary packages:
# - postgresql-client: Required to interact with PostgreSQL databases.
# - build-essential: Contains compilers and libraries needed to build some Python packages.
# - postgresql and musl: Necessary for interacting with PostgreSQL databases and providing a lightweight C standard library.
RUN python -m venv /py && \
    apt-get update && \
    apt-get install -y postgresql-client curl && \
    apt-get install -y --no-install-recommends build-essential postgresql musl && \
    # Upgrade pip to the latest version to ensure all packages are installed with the latest improvements and security fixes.
    /py/bin/pip install --upgrade pip && \
    /py/bin/pip install --upgrade setuptools && \
    # Install Python dependencies from the requirements.txt file located in the /app directory.
    /py/bin/pip install -r /app/requirements.txt && \
    # Clean up by removing unnecessary packages to reduce the size of the final image.
    # This step removes build-essential, postgresql, and musl packages, which are only needed for building the environment.
    apt-get purge -y --auto-remove build-essential postgresql musl && \
    rm -rf /var/lib/apt/lists/* && \
    # Create a new user named 'user' without a home directory and with a disabled password for security.
    # This user will be used to run the application, following the best practice of not running applications as root.
    adduser --disabled-password --no-create-home user

# Add the virtual environment's bin directory to the PATH environment variable.
# This ensures that when you run Python or pip commands, they use the executables from the virtual environment.
ENV PATH="/py/bin:$PATH"

# Switch to the 'user' account created earlier to run the application.
# This enhances security by avoiding running the application as the root user.
USER user
