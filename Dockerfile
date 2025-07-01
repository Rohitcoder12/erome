# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Install ffmpeg and git
RUN apt-get update && apt-get install -y ffmpeg git

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# *** IMPORTANT: Command to run your bot.py script ***
CMD ["python", "bot.py"]