// Copyright Materialize, Inc. and contributors. All rights reserved.
//
// Use of this software is governed by the Business Source License
// included in the LICENSE file.
//
// As of the Change Date specified in that file, in accordance with
// the Business Source License, use of this software will be governed
// by the Apache License, Version 2.0.

// BEGIN LINT CONFIG
// DO NOT EDIT. Automatically generated by bin/gen-lints.
// Have complaints about the noise? See the note in misc/python/materialize/cli/gen-lints.py first.
#![allow(clippy::style)]
#![allow(clippy::complexity)]
#![allow(clippy::large_enum_variant)]
#![allow(clippy::mutable_key_type)]
#![allow(clippy::stable_sort_primitive)]
#![allow(clippy::map_entry)]
#![allow(clippy::box_default)]
#![warn(clippy::bool_comparison)]
#![warn(clippy::clone_on_ref_ptr)]
#![warn(clippy::no_effect)]
#![warn(clippy::unnecessary_unwrap)]
#![warn(clippy::dbg_macro)]
#![warn(clippy::todo)]
#![warn(clippy::wildcard_dependencies)]
#![warn(clippy::zero_prefixed_literal)]
#![warn(clippy::borrowed_box)]
#![warn(clippy::deref_addrof)]
#![warn(clippy::double_must_use)]
#![warn(clippy::double_parens)]
#![warn(clippy::extra_unused_lifetimes)]
#![warn(clippy::needless_borrow)]
#![warn(clippy::needless_question_mark)]
#![warn(clippy::needless_return)]
#![warn(clippy::redundant_pattern)]
#![warn(clippy::redundant_slicing)]
#![warn(clippy::redundant_static_lifetimes)]
#![warn(clippy::single_component_path_imports)]
#![warn(clippy::unnecessary_cast)]
#![warn(clippy::useless_asref)]
#![warn(clippy::useless_conversion)]
#![warn(clippy::builtin_type_shadow)]
#![warn(clippy::duplicate_underscore_argument)]
#![warn(clippy::double_neg)]
#![warn(clippy::unnecessary_mut_passed)]
#![warn(clippy::wildcard_in_or_patterns)]
#![warn(clippy::crosspointer_transmute)]
#![warn(clippy::excessive_precision)]
#![warn(clippy::overflow_check_conditional)]
#![warn(clippy::as_conversions)]
#![warn(clippy::match_overlapping_arm)]
#![warn(clippy::zero_divided_by_zero)]
#![warn(clippy::must_use_unit)]
#![warn(clippy::suspicious_assignment_formatting)]
#![warn(clippy::suspicious_else_formatting)]
#![warn(clippy::suspicious_unary_op_formatting)]
#![warn(clippy::mut_mutex_lock)]
#![warn(clippy::print_literal)]
#![warn(clippy::same_item_push)]
#![warn(clippy::useless_format)]
#![warn(clippy::write_literal)]
#![warn(clippy::redundant_closure)]
#![warn(clippy::redundant_closure_call)]
#![warn(clippy::unnecessary_lazy_evaluations)]
#![warn(clippy::partialeq_ne_impl)]
#![warn(clippy::redundant_field_names)]
#![warn(clippy::transmutes_expressible_as_ptr_casts)]
#![warn(clippy::unused_async)]
#![warn(clippy::disallowed_methods)]
#![warn(clippy::disallowed_macros)]
#![warn(clippy::disallowed_types)]
#![warn(clippy::from_over_into)]
// END LINT CONFIG

//! PostgreSQL utility library.

use openssl::pkey::PKey;
use openssl::ssl::{SslConnector, SslMethod, SslVerifyMode};
use openssl::x509::X509;
use postgres_openssl::MakeTlsConnector;
use tokio_postgres::config::SslMode;
use tracing::warn;

macro_rules! bail_generic {
    ($fmt:expr, $($arg:tt)*) => {
        return Err(PostgresError::Generic(anyhow::anyhow!($fmt, $($arg)*)))
    };
    ($err:expr $(,)?) => {
        return Err(PostgresError::Generic(anyhow::anyhow!($err)))
    };
}

#[cfg(feature = "schemas")]
pub mod desc;
#[cfg(feature = "schemas")]
pub mod schemas;
#[cfg(feature = "schemas")]
pub use schemas::{get_schemas, publication_info};
#[cfg(feature = "tunnel")]
pub mod tunnel;
#[cfg(feature = "tunnel")]
pub use tunnel::{
    drop_replication_slots, Config, ReplicationTimeouts, TunnelConfig,
    DEFAULT_REPLICATION_CONNECT_TIMEOUT, DEFAULT_REPLICATION_KEEPALIVE_IDLE,
    DEFAULT_REPLICATION_KEEPALIVE_INTERVAL, DEFAULT_REPLICATION_KEEPALIVE_RETRIES,
    DEFAULT_REPLICATION_TCP_USER_TIMEOUT,
};

/// An error representing pg, ssh, ssl, and other failures.
#[derive(Debug, thiserror::Error)]
pub enum PostgresError {
    /// Any other error we bail on.
    #[error(transparent)]
    Generic(#[from] anyhow::Error),
    /// Error using ssh.
    #[cfg(feature = "tunnel")]
    #[error(transparent)]
    Ssh(#[from] openssh::Error),
    /// Error doing io to setup an ssh connection.
    #[error(transparent)]
    SshIo(#[from] std::io::Error),
    /// A postgres error.
    #[error(transparent)]
    Postgres(#[from] tokio_postgres::Error),
    /// Error setting up postgres ssl.
    #[error(transparent)]
    PostgresSsl(#[from] openssl::error::ErrorStack),
}

/// Creates a TLS connector for the given [`Config`].
pub fn make_tls(config: &tokio_postgres::Config) -> Result<MakeTlsConnector, PostgresError> {
    let mut builder = SslConnector::builder(SslMethod::tls_client())?;
    // The mode dictates whether we verify peer certs and hostnames. By default, Postgres is
    // pretty relaxed and recommends SslMode::VerifyCa or SslMode::VerifyFull for security.
    //
    // For more details, check out Table 33.1. SSL Mode Descriptions in
    // https://postgresql.org/docs/current/libpq-ssl.html#LIBPQ-SSL-PROTECTION.
    let (verify_mode, verify_hostname) = match config.get_ssl_mode() {
        SslMode::Disable | SslMode::Prefer => (SslVerifyMode::NONE, false),
        SslMode::Require => match config.get_ssl_root_cert() {
            // If a root CA file exists, the behavior of sslmode=require will be the same as
            // that of verify-ca, meaning the server certificate is validated against the CA.
            //
            // For more details, check out the note about backwards compatibility in
            // https://postgresql.org/docs/current/libpq-ssl.html#LIBQ-SSL-CERTIFICATES.
            Some(_) => (SslVerifyMode::PEER, false),
            None => (SslVerifyMode::NONE, false),
        },
        SslMode::VerifyCa => (SslVerifyMode::PEER, false),
        SslMode::VerifyFull => (SslVerifyMode::PEER, true),
        _ => panic!("unexpected sslmode {:?}", config.get_ssl_mode()),
    };

    // Configure peer verification
    builder.set_verify(verify_mode);

    // Configure certificates
    match (config.get_ssl_cert(), config.get_ssl_key()) {
        (Some(ssl_cert), Some(ssl_key)) => {
            builder.set_certificate(&*X509::from_pem(ssl_cert)?)?;
            builder.set_private_key(&*PKey::private_key_from_pem(ssl_key)?)?;
        }
        (None, Some(_)) => {
            bail_generic!("must provide both sslcert and sslkey, but only provided sslkey")
        }
        (Some(_), None) => {
            bail_generic!("must provide both sslcert and sslkey, but only provided sslcert")
        }
        _ => {}
    }
    if let Some(ssl_root_cert) = config.get_ssl_root_cert() {
        builder
            .cert_store_mut()
            .add_cert(X509::from_pem(ssl_root_cert)?)?;
    }

    let mut tls_connector = MakeTlsConnector::new(builder.build());

    // Configure hostname verification
    match (verify_mode, verify_hostname) {
        (SslVerifyMode::PEER, false) => tls_connector.set_callback(|connect, _| {
            connect.set_verify_hostname(false);
            Ok(())
        }),
        _ => {}
    }

    Ok(tls_connector)
}
