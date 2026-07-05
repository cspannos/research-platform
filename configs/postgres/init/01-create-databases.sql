-- Platform metadata database (created by POSTGRES_DB env)
-- Per-tenant databases for durable project metadata

CREATE DATABASE tenant_demo;
CREATE DATABASE tenant_mev;
CREATE DATABASE tenant_anomaly;
CREATE DATABASE tenant_collective;
CREATE DATABASE tenant_exoplanet;

GRANT ALL PRIVILEGES ON DATABASE tenant_demo TO platform;
GRANT ALL PRIVILEGES ON DATABASE tenant_mev TO platform;
GRANT ALL PRIVILEGES ON DATABASE tenant_anomaly TO platform;
GRANT ALL PRIVILEGES ON DATABASE tenant_collective TO platform;
GRANT ALL PRIVILEGES ON DATABASE tenant_exoplanet TO platform;
