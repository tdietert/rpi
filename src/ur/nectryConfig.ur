(* NectryConfig: deployment-level configuration for the Nectry server.
 *
 * trustedTokenSources lists the OIDC issuers whose ID tokens are accepted
 * by the authentication flow. Each entry pairs an issuer (Iss) and a client
 * audience (Aud) with the JWKS endpoint (JwksUri) used to fetch the signing
 * keys for signature verification.
 *
 * To enable Microsoft tenants, add an entry of the shape:
 *   {Iss = "https://login.microsoftonline.com/<tenant-id>/v2.0",
 *    Aud = "<microsoft-client-id>",
 *    JwksUri = "https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys"}
 * The tenant id in Iss and JwksUri must match.
 *)

val trustedTokenSources : list {Iss : string, Aud : string, JwksUri : string} =
    {Iss = "https://accounts.google.com",
     Aud = "<google-client-id>",
     JwksUri = "https://www.googleapis.com/oauth2/v3/certs"} :: []

val allowTestingAccount : bool = True
