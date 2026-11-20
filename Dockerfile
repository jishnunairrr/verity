# Verity harness on a server. Build with this folder as the context:
#   docker build -t verity verity/
#   docker run -p 8765:8765 -e VERITY_ACCESS_CODE=<code> verity
# The browser runs without a window here, so agencies that need a person to
# pass a security check cannot be searched from this image.
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

ENV VERITY_HEADLESS=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765

WORKDIR /app
COPY requirements.txt /app/verity/requirements.txt
RUN pip install --no-cache-dir -r /app/verity/requirements.txt playwright==1.45.0
COPY . /app/verity

EXPOSE 8765
CMD ["python", "-m", "verity", "serve", "--host", "0.0.0.0", "--no-open"]
