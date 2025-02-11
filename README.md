
## How to Run Code:


## Run docker version:
- Ensure you have copied the `.env` file inside the `src` directory . This can be done by running `cp .env.sample .env` in the `src` directory and customise the `.env` file as required.


    - `docker compose up -d --build`

You will only need to run the above command if you are running the project for the first time. After that run `docker compose up -d` for starting and `docker compose down` for stopping the containers.

- You should be able to access the web interface at `localhost:8000/admin`.
- To create a user for testing the web interface, run the below command and follow the prompts:
    - `docker exec -it web python src/manage.py migrate`. 
    - `docker exec -it web python src/manage.py createsuperuser`
- Manually install playwright browsers in the container by running `playwright install` in the container.
    - `docker-compose exec web playwright install`
    
