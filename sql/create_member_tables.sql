-- Run once before the first member load in the target database and schema.
-- MEMBER_RAW matches the columns and normalized date formats emitted by
-- code/load_members.py.
CREATE TABLE IF NOT EXISTS MEMBER_RAW (
    Member_Name VARCHAR NOT NULL,
    Member_Id VARCHAR NOT NULL,
    Enrollment_Date DATE NOT NULL,
    Last_Flight_Date DATE NOT NULL,
    Tier_Code VARCHAR NOT NULL,
    Agent_Name VARCHAR NOT NULL,
    State VARCHAR NOT NULL,
    Country VARCHAR NOT NULL,
    DOB DATE NOT NULL,
    Is_Active VARCHAR NOT NULL
);

-- load_member_staging.sql rebuilds this table with the same profile columns
-- plus the derived Age and Stale_Member values.
CREATE TABLE IF NOT EXISTS MEMBER_STAGING (
    Member_Name VARCHAR,
    Member_Id VARCHAR,
    Enrollment_Date DATE,
    Last_Flight_Date DATE,
    Tier_Code VARCHAR,
    Agent_Name VARCHAR,
    State VARCHAR,
    Country VARCHAR,
    DOB DATE,
    Is_Active VARCHAR,
    Age NUMBER(3, 0),
    Stale_Member BOOLEAN
);
