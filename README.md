# Application Setup and Execution Guide

## Prerequisites

Before you begin, ensure you have the following installed on your system:

- Docker
- Docker Compose

## Environment Configuration

1. **Copy Environment Variables**:
   - Navigate to the `src` directory.
   - Copy the sample environment file to `.env`:
     ```bash
     cp .env.sample .env
     ```
   - Customize the `.env` file as required for your environment.

## Running the Application

### Using Docker

1. **Build and Start Containers**:
   - Run the following command to build and start the Docker containers:
     ```bash
     docker compose up -d --build
     ```
   - This command is required only the first time you run the project. For subsequent starts, use:
     ```bash
     docker compose up -d
     ```

2. **Stopping Containers**:
   - To stop the running containers, execute:
     ```bash
     docker compose down
     ```

3. **Accessing the Web Interface**:
   - Once the containers are running, access the web interface at:
     ```
     http://localhost:8000/admin
     ```

4. **Create a Superuser**:
   - To create a user for testing the web interface, run the following commands:
     ```bash
     docker exec -it web python src/manage.py migrate
     docker exec -it web python src/manage.py createsuperuser
     ```

5. **Install Playwright Browsers**:
   - Manually install Playwright browsers inside the container:
     ```bash
     docker-compose exec web playwright install
     ```

## Application Features

- **Facebook Marketplace Integration**:
  - The application can create and manage listings on Facebook Marketplace.
  - It uses Playwright for browser automation to mimic human interactions.

- **Gumtree Integration**:
  - The application can import and manage listings from Gumtree.

- **User Management**:
  - The application supports user management through Django's admin interface.

## Code Structure

- **`src/VehicleListing/facebook_listing.py`**:
  - Contains functions for extracting and creating Facebook Marketplace listings.
  - Example functions include `extract_facebook_listing_details` and `create_marketplace_listing`.

- **`src/VehicleListing/gumtree_scraper.py`**:
  - Handles scraping and data extraction from Gumtree listings.

- **`src/VehicleListing/models.py`**:
  - Defines the data models for Facebook and Gumtree listings, user credentials, and more.

- **`src/VehicleListing/views.py`**:
  - Contains API views for importing URLs and managing listings.

- **`docker/web/Dockerfile`**:
  - Defines the Docker image setup for the application, including environment setup and dependencies.

## Additional Information

- **Logging**:
  - The application uses logging to track operations and errors. Logs are stored in the `/app/logs` directory inside the Docker container.

- **Security**:
  - The application runs under a non-root user inside the Docker container for enhanced security.

- **Playwright Configuration**:
  - Playwright is configured to run with necessary dependencies and browsers installed within the Docker environment.

## Troubleshooting

- If you encounter issues with Docker, ensure that your Docker daemon is running and that you have the necessary permissions to execute Docker commands.
- For Playwright-related issues, verify that the browsers are correctly installed inside the container.
