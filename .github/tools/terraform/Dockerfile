FROM hashicorp/terraform:1.14.8@sha256:42ecfb253183ec823646dd7859c5652039669409b44daa72abf57112e622849a AS upstream
FROM scratch
COPY --from=upstream /bin/terraform /terraform
