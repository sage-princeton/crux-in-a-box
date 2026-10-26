FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
COPY devops/apt /opt/crux-apt
RUN apt-get update -qq && apt-get upgrade -y -qq \
    && bash /opt/crux-apt/install.sh base \
    && bash /opt/crux-apt/install.sh sdk \
    && bash /opt/crux-apt/install.sh controller \
    && bash /opt/crux-apt/install.sh node \
    && bash /opt/crux-apt/install.sh caddy \
    && rm -rf /var/lib/apt/lists/*
