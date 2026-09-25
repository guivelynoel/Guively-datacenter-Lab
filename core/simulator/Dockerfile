FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY motor_simulator.py .

CMD ["python", "-u", "motor_simulator.py"]
