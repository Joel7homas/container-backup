import requests
import json
import docker
from datetime import datetime
import os
import gzip
import logging
import sys
import schedule
import time
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

class PortainerClient:
    def __init__(self, url, api_key):
        self.url = url.rstrip('/')
        self.api_key = api_key
        if not self.api_key:
            logger.error("Portainer API key is empty")
        else:
            logger.info(f"Portainer API key length: {len(self.api_key)}")
        logger.info(f"Initialized PortainerClient with URL: {url}")

    def get_stacks(self):
        try:
            response = requests.get(
                f"{self.url}/api/stacks",
                headers={"X-API-Key": self.api_key}
            )
            response.raise_for_status()
            return {stack['Name']: stack['Id'] for stack in response.json()}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching stacks: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"Response status code: {e.response.status_code}")
                logger.error(f"Response content: {e.response.content}")
            return {}

    def get_stack_env(self, stack_name, stacks_dict):
        try:
            stack_id = stacks_dict.get(stack_name)
            if not stack_id:
                logger.warning(f"No stack ID found for name: {stack_name}")
                return None

            response = requests.get(
                f"{self.url}/api/stacks/{stack_id}",
                headers={"X-API-Key": self.api_key}
            )
            response.raise_for_status()
            stack_data = response.json()

            env_vars = {}
            if 'Env' in stack_data:
                for env in stack_data['Env']:
                    if isinstance(env, dict) and 'name' in env and 'value' in env:
                        env_vars[env['name']] = env['value']
                    elif isinstance(env, str) and '=' in env:
                        key, value = env.split('=', 1)
                        env_vars[key] = value
            
            # Resolve variable references
            resolved_vars = env_vars.copy()
            for key, value in env_vars.items():
                if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                    ref_var = value[2:-1]
                    if ref_var in env_vars:
                        resolved_vars[key] = env_vars[ref_var]
            return resolved_vars
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching stack details: {str(e)}")
            return None

class DatabaseBackup:
    def __init__(self, portainer_client):
        try:
            self.docker_client = docker.from_env()
            self.portainer = portainer_client
            self.stacks = self.portainer.get_stacks()
            self.exclude_stacks = os.environ.get('EXCLUDE_FROM_BACKUP', '').split()
            logger.info("Successfully connected to Docker daemon")
            logger.info(f"Found {len(self.stacks)} stacks in Portainer")
            logger.info(f"Excluding stacks: {', '.join(self.exclude_stacks) if self.exclude_stacks else 'None'}")
        except Exception as e:
            logger.error(f"Failed to initialize: {str(e)}")
            raise

    def get_container_credentials(self, container):
        try:
            stack_name = container.labels.get('com.docker.compose.project')
            if not stack_name:
                logger.warning(f"No stack name found for container: {container.name}")
                return None

            if stack_name in self.exclude_stacks:
                logger.info(f"Skipping container {container.name} (stack {stack_name} is excluded)")
                return None

            logger.info(f"Fetching credentials for stack name: {stack_name}")
            env_vars = self.portainer.get_stack_env(stack_name, self.stacks)
            if not env_vars:
                logger.warning(f"No environment variables found for stack: {stack_name}")
                return None

            db_type = self._get_database_type(container)
            logger.info(f"Container {container.name} identified as {db_type} database")

            def find_first_value(env_vars, possible_keys):
                for key in possible_keys:
                    if key in env_vars:
                        return env_vars[key]
                return None

            if db_type in ['postgres', 'pgvecto']:
                user_keys = [
                    'DB_USER', 'POSTGRES_USER', 'PGUSER',
                    'DATABASE_USER', 'POSTGRESQL_USER',
                    f'{stack_name.upper()}_DB_USER',
                    'DB_USERNAME',
                    f'{stack_name.upper()}_DBUSER',
                    'POSTGRES_NON_ROOT_USER'
                ]
                password_keys = [
                    'DB_PASSWORD', 'POSTGRES_PASSWORD', 'PGPASSWORD',
                    'DATABASE_PASSWORD', 'POSTGRESQL_PASSWORD',
                    f'{stack_name.upper()}_DB_PASSWORD',
                    f'{stack_name.upper()}_DBPASS',
                    'POSTGRES_NON_ROOT_PASSWORD'
                ]
                database_keys = [
                    'DB_NAME', 'POSTGRES_DB', 'DB_DATABASE',
                    'DATABASE_NAME', 'POSTGRESQL_DATABASE',
                    f'{stack_name.upper()}_DB_NAME',
                    'DB_DATABASE_NAME',
                    f'{stack_name.upper()}_DBNAME'
                ]

                # First try direct environment variables
                user = find_first_value(env_vars, user_keys)
                password = find_first_value(env_vars, password_keys)
                database = find_first_value(env_vars, database_keys)
                
                logger.info(f"Initial values from env - User: {user}, DB: {database}")

                # Handle $-prefixed variables in direct env vars
                if user and user.startswith('$'):
                    user = env_vars.get(user[1:])
                if password and password.startswith('$'):
                    password = env_vars.get(password[1:])
                if database and database.startswith('$'):
                    database = env_vars.get(database[1:])

                # If direct vars didn't work, try connection string
                if not all([user, password, database]):
                    conn_string_keys = ['DB_URI', 'POSTGRES_CONNECTION_STRING', f'{stack_name.upper()}_DATABASE_URL']
                    conn_string = find_first_value(env_vars, conn_string_keys)
                    
                    if conn_string:
                        # Replace any ${var} or $var in connection string with actual values
                        for key, value in env_vars.items():
                            conn_string = conn_string.replace(f'${key}', value)
                            conn_string = conn_string.replace(f'${{{key}}}', value)
                        
                        try:
                            parsed = urlparse(conn_string)
                            user = user or parsed.username
                            password = password or parsed.password
                            database = database or parsed.path.strip('/')
                            logger.info(f"Values from connection string - User: {user}, DB: {database}")
                        except Exception as e:
                            logger.error(f"Failed to parse connection string: {e}")

                logger.info(f"Final values - User: {user}, DB: {database}")

            elif db_type in ['mysql', 'mariadb']:
                # Always use root credentials for MySQL/MariaDB
                root_password_keys = [
                    'MYSQL_ROOT_PASSWORD',
                    'DB_ROOT_PASSWD',
                    f'INIT_{stack_name.upper()}_MYSQL_ROOT_PASSWORD'
                ]
                
                user = 'root'
                password = find_first_value(env_vars, root_password_keys)
                
                # Get all databases if no specific database is specified
                database_keys = [
                    'DB_NAME', 'MYSQL_DATABASE', 'DB_DATABASE',
                    'DATABASE_NAME', 'MARIADB_DATABASE',
                    f'{stack_name.upper()}_DB_NAME',
                    f'{stack_name.upper()}_MYSQL_DB_CCNET_DB_NAME',
                    f'{stack_name.upper()}_MYSQL_DB_SEAFILE_DB_NAME',
                    f'{stack_name.upper()}_MYSQL_DB_SEAHUB_DB_NAME'
                ]
                database = find_first_value(env_vars, database_keys)

            logger.info(f"Found credentials for {container.name}:")
            logger.info(f"User found: {bool(user)}")
            logger.info(f"Password found: {bool(password)}")
            logger.info(f"Database found: {bool(database)}")
            logger.info(f"Available env vars: {', '.join(env_vars.keys())}")

            if not all([user, password]):
                logger.error(f"Missing required credentials for {container.name}")
                return None

            return {'user': user, 'password': password, 'database': database}

        except Exception as e:
            logger.error(f"Error getting credentials for {container.name}: {str(e)}")
            return None

    def backup_database(self, container):
        try:
            credentials = self.get_container_credentials(container)
            if not credentials:
                return

            db_type = self._get_database_type(container)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"/backups/{container.name}_{timestamp}.sql.gz"

            logger.info(f"Starting backup of {container.name} to {backup_file}")

            if db_type in ['postgres', 'pgvecto']:
                cmd = f"pg_dump -U {credentials['user']} {credentials['database']}"
                env = {"PGPASSWORD": credentials['password']}
            elif db_type in ['mysql', 'mariadb']:
                cmd = f"mysqldump -u {credentials['user']} -p{credentials['password']}"
                if credentials['database']:
                    cmd += f" {credentials['database']}"
                else:
                    cmd += " --all-databases"
                env = {}
            elif db_type == 'sqlite':
                self._backup_sqlite(container, timestamp)
                return

            result = container.exec_run(cmd, environment=env)
            if result.exit_code != 0:
                logger.error(f"Backup command failed for {container.name}: {result.output.decode()}")
                return

            with gzip.open(backup_file, 'wb') as f:
                f.write(result.output)
            logger.info(f"Successfully backed up {container.name}")

        except Exception as e:
            logger.error(f"Error backing up {container.name}: {str(e)}")

    def _backup_sqlite(self, container, timestamp):
        try:
            cmd = "find / -name '*.sqlite' -o -name '*.db' -o -name '*.sqlite3'"
            result = container.exec_run(cmd)
            db_files = result.output.decode().splitlines()

            for db_file in db_files:
                backup_name = f"/backups/{container.name}_{os.path.basename(db_file)}_{timestamp}.sqlite.gz"
                logger.info(f"Backing up SQLite database {db_file} to {backup_name}")

                dump_cmd = f"sqlite3 {db_file} '.backup /tmp/temp.db'"
                result = container.exec_run(dump_cmd)
                if result.exit_code != 0:
                    logger.error(f"SQLite backup failed for {db_file}: {result.output.decode()}")
                    continue

                with container.get_archive('/tmp/temp.db')[0] as source, gzip.open(backup_name, 'wb') as dest:
                    for chunk in source:
                        dest.write(chunk)

                container.exec_run("rm /tmp/temp.db")
                logger.info(f"Successfully backed up SQLite database {db_file}")
        except Exception as e:
            logger.error(f"Error backing up SQLite database: {str(e)}")

    def _get_database_type(self, container):
        try:
            image = container.image.tags[0].lower()
            for db_type in ['postgres', 'pgvecto', 'mysql', 'mariadb', 'sqlite']:
                if db_type in image:
                    return db_type
            return None
        except Exception as e:
            logger.error(f"Error determining database type for {container.name}: {str(e)}")
            return None

    def run(self):
        try:
            containers = self.docker_client.containers.list()
            logger.info(f"Found {len(containers)} containers")

            for container in containers:
                if self._get_database_type(container):
                    logger.info(f"Starting backup process for {container.name}")
                    self.backup_database(container)
                else:
                    logger.debug(f"Skipping non-database container: {container.name}")
        except Exception as e:
            logger.error(f"Error in backup run: {str(e)}")

def main():
    try:
        required_vars = ['PORTAINER_URL', 'PORTAINER_API_KEY']
        missing_vars = [var for var in required_vars if var not in os.environ]
        if missing_vars:
            logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            sys.exit(1)

        portainer = PortainerClient(
            os.environ['PORTAINER_URL'],
            os.environ['PORTAINER_API_KEY']
        )

        backup = DatabaseBackup(portainer)

        cron_schedule = os.environ.get('CRON_SCHEDULE')
        if cron_schedule:
            logger.info(f"Standing by to run at {cron_schedule}")
            schedule.every().day.at(cron_schedule).do(backup.run)
            while True:
                schedule.run_pending()
                # Sleep until next scheduled run
                time_until_next = schedule.idle_seconds()
                if time_until_next is not None:
                    time.sleep(time_until_next)
                else:
                    time.sleep(3600)  # Sleep for an hour if no jobs scheduled
        else:
            backup.run()

    except Exception as e:
        logger.error(f"Fatal error in main: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()

