import sys
import time

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from django.db.utils import OperationalError

class Command(BaseCommand):
    """
    A Django management command to block the startup of the container until the database becomes available.

    This command continuously attempts to establish a connection to the database. It will retry
    for a specified number of attempts or until a successful connection is made. If the database
    does not become available within the allowed attempts, the command will exit with an error.
    """

    help = "This command is used to block the container start up until the Database becomes available"

    def add_arguments(self, parser):
        """
        Adds command line arguments for configuring the polling behavior.

        Args:
            parser (CommandParser): The command parser instance to which arguments should be added.
        """
        parser.add_argument("--poll_seconds", type=float, default=3,
                            help="Number of seconds to wait between retries (default: 3).")
        parser.add_argument("--max_retries", type=int, default=10,
                            help="Maximum number of retries before giving up (default: 10).")

    def handle(self, *args, **options):
        """
        Handles the command execution, retrying the database connection until it is available.

        Args:
            *args: Variable length argument list.
            **options: Keyword arguments from the command line arguments.

        Exits with status code 1 if the database does not become available within the allowed retries.
        """
        max_retries = options['max_retries']
        poll_seconds = options['poll_seconds']

        for retry in range(max_retries):
            try:
                connection.ensure_connection()
            except OperationalError as ex:
                self.stdout.write(
                    "Database unavailable due to: {error}".format(error=ex)
                )
                time.sleep(poll_seconds)
            else:
                self.stdout.write("Database connected successfully.")
                break
        else:
            self.stdout.write(self.style.ERROR("Database unavailable. Check the logs above for more information"))
            sys.exit(1)
