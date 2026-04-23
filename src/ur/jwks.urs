type jwk = {Kid : string, Kty : string, Alg : option string,
            N : string, E : string}

(* Returns the currently-trusted JWKS for an issuer, fetching and caching
   if absent or expired. Returns None iff the issuer is not in
   NectryConfig.trustedTokenSources, or fetch/parse fails. Returned list
   is filtered to RSA keys whose Alg is None or Some "RS256". *)
val getKeys : string -> transaction (option (list jwk))
