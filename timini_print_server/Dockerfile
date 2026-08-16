# Home Assistant Add-on: TiMini Print Server
#
# NOTE: built by your Supervisor at install time - needs internet access
# to git-clone TiMini-Print and install its Python dependencies. Written
# from TiMini-Print's public README/CLI docs; not tested by the assistant
# that wrote it against physical hardware or a real HAOS install.

ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
        python3 \
        py3-pip \
        bluez \
        bluez-deprecated \
        dbus \
        git \
        fontconfig \
        font-dejavu

# DejaVu Sans has full Unicode coverage (including Hungarian ő/ű and
# other Latin Extended-A accented characters) - without any system font
# installed, whatever text-rendering TiMini-Print's --text mode uses
# was falling back to a minimal placeholder font that only covers a
# handful of accented Latin-1 characters, printing "tofu" boxes for
# ő/ű specifically.
RUN fc-cache -f || true

RUN apk add --no-cache bluez-btmgmt || true
RUN apk add --no-cache bluez-tools || true
RUN apk add --no-cache bluez-progs || true

WORKDIR /opt

RUN git clone --depth 1 https://github.com/Dejniel/TiMini-Print.git timini-print

WORKDIR /opt/timini-print

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY wrapper.py /opt/timini-print/wrapper.py
COPY run.sh /
RUN chmod a+x /run.sh

EXPOSE 8096

CMD [ "/run.sh" ]
