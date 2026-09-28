FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /usr/sbin/nologin crux-inspect \
    && passwd -d crux-inspect \
    && mkdir -p /run/sshd /srv/crux-inspection/exports /etc/ssh/authorized_keys \
    && printf 'fixture evidence\n' > /srv/crux-inspection/exports/activity.txt \
    && ln -s /etc/passwd /srv/crux-inspection/exports/escape \
    && chmod 755 /srv/crux-inspection /srv/crux-inspection/exports \
    && printf '%s\n' 'Match User crux-inspect' '  ChrootDirectory /srv/crux-inspection' \
       '  ForceCommand internal-sftp -R' '  DisableForwarding yes' '  PermitTTY no' \
       '  PermitUserRC no' '  PasswordAuthentication no' '  AuthenticationMethods publickey' \
       '  AuthorizedKeysFile /etc/ssh/authorized_keys/%u' > /etc/ssh/sshd_config.d/inspection.conf
COPY tests/sshd-entrypoint.sh /entrypoint.sh
CMD ["sh", "/entrypoint.sh"]
