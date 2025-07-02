# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies, including those for Playwright browsers
RUN apt-get update && apt-get install -y ffmpeg git

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# --- NEW AND CRITICAL STEP ---
# Install Playwright's browser dependencies
RUN playwright install --with-deps
# -----------------------------

# Copy the rest of the application code into the container
COPY . .

# Command to run your bot
CMD ["python", "bot.py"]