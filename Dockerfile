# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Print the contents of the requirements.txt file for debugging
RUN cat requirements.txt

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the .env file to the container
COPY .env .env

# Make port 8443 available to the world outside this container
ENV HOST=0.0.0.0

EXPOSE 8080

# Run the application
CMD ["python", "main.py", "--host=0.0.0.0"]
