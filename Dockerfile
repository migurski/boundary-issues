# Stage 1: Build the Planetiler fat JAR
FROM --platform=linux/arm64 eclipse-temurin:21-jdk AS builder

RUN apt-get update -y \
 && apt-get install -y maven \
 && apt-get clean -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY webhook/tiles/ /build/

RUN mvn clean package -DskipTests

# Stage 2: Runtime (Lambda)
FROM --platform=linux/arm64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update -y \
 && apt install -y python3 python3-pip python3-gdal python3-numpy git openjdk-21-jre-headless \
 && apt clean -y \
 && rm -rf /var/lib/apt/lists/*

ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip3 install 'awslambdaric==2.2.1' 'boto3==1.34.34'

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt

# Copy the processor handler
WORKDIR /var/task
COPY processor.py /var/task/processor.py

COPY --from=builder /build/target/political-views-tiles-1.0.0-with-deps.jar /var/task/tiles.jar

# Bundle landcover source so Planetiler doesn't download it at runtime
COPY webhook/tiles/data/sources/daylight-landcover.gpkg /var/task/data/sources/daylight-landcover.gpkg

# Set the Lambda handler using awslambdaric
ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["processor.handler"]
