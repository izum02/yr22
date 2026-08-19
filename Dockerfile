FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app

# ffmpeg provides both ffmpeg and ffprobe.
RUN echo 'YXB0LWdldCB1cGRhdGUgJiYgYXB0LWdldCBpbnN0YWxsIC15IC0tbm8taW5zdGFsbC1yZWNvbW1lbmRzIGNhLWNlcnRpZmljYXRlcyBjdXJsIGZmbXBlZyAmJiBybSAtcmYgL3Zhci9saWIvYXB0L2xpc3RzLyo=' | base64 -d | sh

# Deno is the recommended JS runtime for yt-dlp EJS.
# ytdlP's current EJS guide requires Deno >= 2.3.
RUN apt-get update \
    && apt-get install -y --no-install-recommends p7zip-full curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

RUN python -m pip install --upgrade pip \
    && echo 'Rmxhc2s+PTMuMSw8NA0KZ3VuaWNvcm4+PTIzLDwyNA0KcmVxdWVzdHM+PTIuMzIsPDMNCnl0LWRscFtkZWZhdWx0LGN1cmwtY2ZmaV0NCnl0LWRscC1lanMNCmNlcnRpZmkNCmJyb3RsaQ0Kd2Vic29ja2V0cw0KcHljcnlwdG9kb21l' | base64 -d | pip install -r /dev/stdin

COPY app.py .
COPY cookie.txt ./cookie.txt

# Useful build-time sanity checks.
RUN $c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("ZmZtcGVnIC12ZXJzaW9uCmZmcHJvYmUgLXZlcnNpb24KcHl0aG9uIC1tIHl0X2RscCAtLXZlcnNpb24KcHl0aG9uIC1tIHl0X2RscCAtLWxpc3QtaW1wZXJzb25hdGUtdGFyZ2V0cwpkZW5vIC0tdmVyc2lvbg=="));$c-split"`n"|%{iex $_}

EXPOSE 10000

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "4", "--timeout", "900", "app:app"]
