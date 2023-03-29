# Copyright Materialize, Inc. and contributors. All rights reserved.
#
# Use of this software is governed by the Business Source License
# included in the LICENSE file at the root of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
from textwrap import dedent
from typing import List

from materialize.checks.actions import Testdrive
from materialize.checks.checks import Check
from materialize.util import MzVersion


class Owners(Check):
    def _create_objects(self, role: str, i: int, expensive: bool = False) -> str:
        s = dedent(
            f"""
            $ postgres-execute connection=postgres://{role}@materialized:6875/materialize
            CREATE DATABASE owner_db{i}
            CREATE SCHEMA owner_schema{i}
            CREATE CONNECTION owner_kafka_conn{i} FOR KAFKA BROKER '${{testdrive.kafka-addr}}'
            CREATE CONNECTION owner_csr_conn{i} FOR CONFLUENT SCHEMA REGISTRY URL '${{testdrive.schema-registry-url}}'
            CREATE TYPE owner_type{i} AS LIST (ELEMENT TYPE = text)
            CREATE TABLE owner_t{i} (c1 int, c2 owner_type{i})
            CREATE INDEX owner_i{i} ON owner_t{i} (c2)
            CREATE VIEW owner_v{i} AS SELECT * FROM owner_t{i}
            CREATE MATERIALIZED VIEW owner_mv{i} AS SELECT * FROM owner_t{i}
            CREATE SECRET owner_secret{i} AS 'MY_SECRET'
            """
        )
        if expensive:
            s += dedent(
                f"""
                CREATE SOURCE owner_source{i} FROM LOAD GENERATOR COUNTER (SCALE FACTOR 0.01)
                CREATE SINK owner_sink{i} FROM owner_mv{i} INTO KAFKA CONNECTION owner_kafka_conn{i} (TOPIC 'sink-sink-owner{i}') FORMAT AVRO USING CONFLUENT SCHEMA REGISTRY CONNECTION owner_csr_conn{i} ENVELOPE DEBEZIUM
                CREATE CLUSTER owner_cluster{i} REPLICAS (owner_cluster_r{i} (SIZE '4'))
                """
            )

        return s

    def _drop_objects(self, i: int) -> str:
        return dedent(
            f"""
            > DROP SECRET owner_secret{i}
            > DROP MATERIALIZED VIEW owner_mv{i}
            > DROP VIEW owner_v{i}
            > DROP INDEX owner_i{i}
            > DROP TABLE owner_t{i}
            > DROP TYPE owner_type{i}
            > DROP CONNECTION owner_csr_conn{i}
            > DROP CONNECTION owner_kafka_conn{i}
            > DROP SCHEMA owner_schema{i}
            > DROP DATABASE owner_db{i}
            """
        )

    def _can_run(self) -> bool:
        return self.base_version >= MzVersion.parse("0.47.0-dev")

    def initialize(self) -> Testdrive:
        return Testdrive(
            "> CREATE ROLE owner_role_01"
            + self._create_objects("owner_role_01", 1, expensive=True)
        )

    def manipulate(self) -> List[Testdrive]:
        return [
            Testdrive(s)
            for s in [
                self._create_objects("owner_role_01", 2)
                + "> CREATE ROLE owner_role_02",
                self._create_objects("owner_role_01", 3)
                + self._create_objects("owner_role_02", 4)
                + "> CREATE ROLE owner_role_03",
            ]
        ]

    def validate(self) -> Testdrive:
        owner1 = (
            "default_owner"
            if self.base_version < MzVersion.parse("0.48.0-dev")
            else "owner_role_01"
        )
        # TODO: Fix owners in dbs, schemas, types after #18414 is fixed
        return Testdrive(
            self._create_objects("owner_role_01", 5)
            + self._create_objects("owner_role_02", 6)
            + self._create_objects("owner_role_03", 7)
            + dedent(
                f"""
                $ psql-execute command="\\l owner_db*"
                \                             List of databases
                   Name    |     Owner     | Encoding | Collate | Ctype | Access privileges
                -----------+---------------+----------+---------+-------+-------------------
                 owner_db1 | {owner1} | UTF8     | C       | C     |
                 owner_db2 | {owner1} | UTF8     | C       | C     |
                 owner_db3 | owner_role_01 | UTF8     | C       | C     |
                 owner_db4 | owner_role_02 | UTF8     | C       | C     |
                 owner_db5 | owner_role_01 | UTF8     | C       | C     |
                 owner_db6 | owner_role_02 | UTF8     | C       | C     |
                 owner_db7 | owner_role_03 | UTF8     | C       | C     |

                $ psql-execute command="\\dn owner_schema*"
                \        List of schemas
                     Name      |     Owner
                ---------------+---------------
                 owner_schema1 | {owner1}
                 owner_schema2 | {owner1}
                 owner_schema3 | owner_role_01
                 owner_schema4 | owner_role_02
                 owner_schema5 | owner_role_01
                 owner_schema6 | owner_role_02
                 owner_schema7 | owner_role_03

                $ psql-execute command="\\dt owner_t*"
                \             List of relations
                 Schema |   Name   | Type  |     Owner
                --------+----------+-------+---------------
                 public | owner_t1 | table | {owner1}
                 public | owner_t2 | table | {owner1}
                 public | owner_t3 | table | owner_role_01
                 public | owner_t4 | table | owner_role_02
                 public | owner_t5 | table | owner_role_01
                 public | owner_t6 | table | owner_role_02
                 public | owner_t7 | table | owner_role_03

                $ psql-execute command="\\di owner_i*"
                \                  List of relations
                 Schema |   Name   | Type  |     Owner     |  Table
                --------+----------+-------+---------------+----------
                 public | owner_i1 | index | {owner1} | owner_t1
                 public | owner_i2 | index | {owner1} | owner_t2
                 public | owner_i3 | index | owner_role_01 | owner_t3
                 public | owner_i4 | index | owner_role_02 | owner_t4
                 public | owner_i5 | index | owner_role_01 | owner_t5
                 public | owner_i6 | index | owner_role_02 | owner_t6
                 public | owner_i7 | index | owner_role_03 | owner_t7

                $ psql-execute command="\\dv owner_v*"
                \            List of relations
                 Schema |   Name   | Type |     Owner
                --------+----------+------+---------------
                 public | owner_v1 | view | {owner1}
                 public | owner_v2 | view | {owner1}
                 public | owner_v3 | view | owner_role_01
                 public | owner_v4 | view | owner_role_02
                 public | owner_v5 | view | owner_role_01
                 public | owner_v6 | view | owner_role_02
                 public | owner_v7 | view | owner_role_03

                $ psql-execute command="\\dmv owner_mv*"
                \                   List of relations
                 Schema |   Name    |       Type        |     Owner
                --------+-----------+-------------------+---------------
                 public | owner_mv1 | materialized view | {owner1}
                 public | owner_mv2 | materialized view | {owner1}
                 public | owner_mv3 | materialized view | owner_role_01
                 public | owner_mv4 | materialized view | owner_role_02
                 public | owner_mv5 | materialized view | owner_role_01
                 public | owner_mv6 | materialized view | owner_role_02
                 public | owner_mv7 | materialized view | owner_role_03

                > SELECT mz_types.name, mz_roles.name FROM mz_types JOIN mz_roles ON mz_types.owner_id = mz_roles.id WHERE mz_types.name LIKE 'owner_type%'
                owner_type1 {owner1}
                owner_type2 {owner1}
                owner_type3 owner_role_01
                owner_type4 owner_role_02
                owner_type5 owner_role_01
                owner_type6 owner_role_02
                owner_type7 owner_role_03

                > SELECT mz_secrets.name, mz_roles.name FROM mz_secrets JOIN mz_roles ON mz_secrets.owner_id = mz_roles.id WHERE mz_secrets.name LIKE 'owner_secret%'
                owner_secret1 {owner1}
                owner_secret2 {owner1}
                owner_secret3 owner_role_01
                owner_secret4 owner_role_02
                owner_secret5 owner_role_01
                owner_secret6 owner_role_02
                owner_secret7 owner_role_03

                > SELECT mz_sources.name, mz_roles.name FROM mz_sources JOIN mz_roles ON mz_sources.owner_id = mz_roles.id WHERE mz_sources.name LIKE 'owner_source%' AND type = 'load-generator'
                owner_source1 {owner1}

                > SELECT mz_sinks.name, mz_roles.name FROM mz_sinks JOIN mz_roles ON mz_sinks.owner_id = mz_roles.id WHERE mz_sinks.name LIKE 'owner_sink%'
                owner_sink1 {owner1}

                > SELECT mz_clusters.name, mz_roles.name FROM mz_clusters JOIN mz_roles ON mz_clusters.owner_id = mz_roles.id WHERE mz_clusters.name LIKE 'owner_cluster%'
                owner_cluster1 {owner1}

                > SELECT mz_cluster_replicas.name, mz_roles.name FROM mz_cluster_replicas JOIN mz_roles ON mz_cluster_replicas.owner_id = mz_roles.id WHERE mz_cluster_replicas.name LIKE 'owner_cluster_r%'
                owner_cluster_r1 {owner1}
                """
            )
            + self._drop_objects(5)
            + self._drop_objects(6)
            + self._drop_objects(7)
        )