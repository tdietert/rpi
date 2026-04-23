open Json

cookie accessToken : string
cookie idToken : string

table auth : { Token : string,
               Expiration : time,
               Email : string }
  PRIMARY KEY Token

type id_token_body = {Issuer : string, Audience : string,
                      Expiration : int, Email : option string}

val _ : Json.json id_token_body =
    Json.json_record_withOptional
        {Issuer = "iss", Audience = "aud", Expiration = "exp"}
        {Email = "email"}

type jwt_header = {Alg : string, Kid : string}
val _ : Json.json jwt_header = Json.json_record {Alg = "alg", Kid = "kid"}

fun base64urlToBlob (s : string) : option blob =
    let
        fun pad s =
            case String.length s % 4 of
                0 => s
              | n => s ^ String.mkString "=" (4 - n)
        val translated = String.mp (fn c =>
                                       case c of
                                           #"-" => #"+"
                                         | #"_" => #"/"
                                         | _ => c) s
    in
        WorldFfi.base64Decode (pad translated)
    end

fun verifyAndDecode (tokO : option string) (tm : time)
    : transaction (option id_token_body) =
    case tokO of
        None => return None
      | Some tok =>
        case String.split tok #"." of
            None => return None
          | Some (headerB64, rest) =>
            case String.split rest #"." of
                None => return None
              | Some (payloadB64, sigB64) =>
                case base64urlToBlob headerB64 of
                    None => return None
                  | Some headerBlob =>
                    case Result.toOption
                             (Json.fromJsonR (blobToString headerBlob)
                              : result jwt_header) of
                        None => return None
                      | Some header =>
                        if header.Alg <> "RS256" then
                            return None
                        else
                            case base64urlToBlob payloadB64 of
                                None => return None
                              | Some payloadBlob =>
                                case Result.toOption
                                         (Json.fromJsonR
                                              (blobToString payloadBlob)
                                          : result id_token_body) of
                                    None => return None
                                  | Some body =>
                                    case List.find
                                             (fn s => s.Iss = body.Issuer
                                                      && s.Aud = body.Audience)
                                             NectryConfig.trustedTokenSources of
                                        None => return None
                                      | Some _ =>
                                        keysO <- Jwks.getKeys body.Issuer;
                                        case keysO of
                                            None => return None
                                          | Some keys =>
                                            case List.find
                                                     (fn k => k.Kid = header.Kid)
                                                     keys of
                                                None => return None
                                              | Some key =>
                                                case base64urlToBlob sigB64 of
                                                    None => return None
                                                  | Some sigBytes =>
                                                    let
                                                        val signingInput =
                                                            headerB64 ^ "."
                                                            ^ payloadB64
                                                    in
                                                        if not (WorldFfi.verify_rs256_jwk
                                                                    key.N key.E
                                                                    signingInput
                                                                    sigBytes)
                                                        then return None
                                                        else
                                                            let
                                                                val exp =
                                                                    fromMilliseconds
                                                                        (body.Expiration
                                                                         * 1000)
                                                            in
                                                                if not (exp > tm) then
                                                                    return None
                                                                else
                                                                    return (Some body)
                                                            end
                                                    end

fun getUsername () : transaction (option string) =
    accessTokO <- getCookie accessToken;
    tm <- now;
    emailO <-
        case accessTokO of
            None => return None
          | Some accessTok =>
            cached <- oneOrNoRows1 (SELECT auth.Expiration, auth.Email
                                    FROM auth
                                    WHERE auth.Token = {[accessTok]});
            case cached of
                Some row =>
                  if row.Expiration > tm then
                      return (Some row.Email)
                  else
                      return None
              | None => return None;
    case (emailO, accessTokO) of
        (Some email, _) => return (Some email)
      | (None, None) =>
        if NectryConfig.allowTestingAccount then
            return (Some "test@nectry.com")
        else
            return None
      | (None, Some accessTok) =>
        idTokO <- getCookie idToken;
        bodyO <- verifyAndDecode idTokO tm;
        case bodyO of
            None => return None
          | Some body =>
            case body.Email of
                None => return None
              | Some email =>
                dml (DELETE FROM auth WHERE auth.Token = {[accessTok]});
                dml (INSERT INTO auth(Token, Expiration, Email)
                      VALUES ({[accessTok]},
                              {[fromMilliseconds (body.Expiration * 1000)]},
                              {[email]}));
                return (Some email)
