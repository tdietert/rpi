open Json

type jwk = {Kid : string, Kty : string, Alg : option string,
            N : string, E : string}

type jwks = {Keys : list jwk}

table jwks_cache : { Issuer : string,
                     KeysJson : string,
                     Expiration : time }
  PRIMARY KEY Issuer

val JWKS_TTL : int = 3600

val _ : Json.json jwk =
    Json.json_record_withOptional
        {Kid = "kid", Kty = "kty", N = "n", E = "e"}
        {Alg = "alg"}

val _ : Json.json jwks = Json.json_record {Keys = "keys"}

fun filter_rsa (keys : list jwk) : list jwk =
    List.filter (fn k => k.Kty = "RSA"
                         && (case k.Alg of
                                 None => True
                               | Some a => a = "RS256")) keys

fun refreshFromNetwork (issuer : string)
                       (src : {Iss : string, Aud : string, JwksUri : string})
                       (tm : time) : transaction (option (list jwk)) =
    respO <- WorldFfi.getOpt src.JwksUri WorldFfi.emptyHeaders True;
    case respO of
        None => return None
      | Some resp =>
          case Result.toOption (Json.fromJsonR resp : result jwks) of
              None => return None
            | Some parsed =>
              let
                  val newExp = fromMilliseconds (toMilliseconds tm + JWKS_TTL * 1000)
              in
                  dml (DELETE FROM jwks_cache
                        WHERE jwks_cache.Issuer = {[issuer]});
                  dml (INSERT INTO jwks_cache(Issuer, KeysJson, Expiration)
                        VALUES ({[issuer]}, {[resp]}, {[newExp]}));
                  return (Some (filter_rsa parsed.Keys))
              end

fun getKeys (issuer : string) : transaction (option (list jwk)) =
    case List.find (fn s => s.Iss = issuer) NectryConfig.trustedTokenSources of
        None => return None
      | Some src =>
        tm <- now;
        cached <- oneOrNoRows1 (SELECT jwks_cache.KeysJson, jwks_cache.Expiration
                                FROM jwks_cache
                                WHERE jwks_cache.Issuer = {[issuer]});
        case cached of
            Some row =>
              if row.Expiration > tm then
                  case Result.toOption (Json.fromJsonR row.KeysJson : result jwks) of
                      Some parsed => return (Some (filter_rsa parsed.Keys))
                    | None => refreshFromNetwork issuer src tm
              else
                  refreshFromNetwork issuer src tm
          | None => refreshFromNetwork issuer src tm
