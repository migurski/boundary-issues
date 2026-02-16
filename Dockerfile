FROM --platform=linux/arm64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update -y \
 && apt install -y python3 python3-pip python3-gdal python3-numpy git \
 && apt clean -y \
 && rm -rf /var/lib/apt/lists/*

ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip3 install 'awslambdaric==2.2.1' 'boto3==1.34.34'

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt

# Copy the processor handler
WORKDIR /var/task
COPY processor.py /var/task/processor.py

# Set the Lambda handler using awslambdaric
ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["processor.handler"]
