FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY datalake_mcp/ ./datalake_mcp/
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "datalake_mcp.server"]
