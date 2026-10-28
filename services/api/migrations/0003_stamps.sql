-- Stamps: the parts whose size grows with the number of cameras (video, state, recording index) come in copies, and
-- each tenant is served by one copy. The columns are added with a default (stamp 1) so that the first version of the
-- API, which does not know them, keeps working on the same database while the paths are moved one by one.
CREATE TABLE stamps (
    id                   integer PRIMARY KEY,
    name                 text NOT NULL,
    -- how this service reaches the stamp's database; the password is in the file named in database_password_file
    database_dsn         text NOT NULL,
    database_password_file text,
    -- the stamp's broker and the file with its dynamic-security admin password
    mqtt_host            text NOT NULL,
    mqtt_port            integer NOT NULL DEFAULT 1883,
    mqtt_admin_password_file text,
    -- the stamp's MediaMTX Control API
    mediamtx_control_url text NOT NULL,
    -- addresses given to browsers and cameras
    public_hls_url       text NOT NULL,
    public_playback_url  text NOT NULL,
    public_rtsp_url      text NOT NULL,
    public_mqtt_url      text NOT NULL,
    -- the audience that the stamp's MediaMTX requires in video tokens
    jwt_audience         text NOT NULL
);

-- Stamp 1 is what the first version already was: the database of this same service and the broker and MediaMTX of the
-- compose file. The API writes the current addresses into this row at every start (stamps.py).
INSERT INTO stamps (id, name, database_dsn, mqtt_host, mediamtx_control_url, public_hls_url, public_playback_url,
                    public_rtsp_url, public_mqtt_url, jwt_audience)
VALUES (1, 'stamp 1', 'postgresql://ccc@postgres:5432/ccc', 'mosquitto', 'http://mediamtx:9997',
        'http://localhost:8888', 'http://localhost:9996', 'rtsp://mediamtx:8554', 'mqtt://mosquitto:1883', 'mediamtx');

ALTER TABLE tenants ADD COLUMN stamp_id integer NOT NULL DEFAULT 1 REFERENCES stamps (id);
ALTER TABLE cameras ADD COLUMN stamp_id integer NOT NULL DEFAULT 1 REFERENCES stamps (id);
