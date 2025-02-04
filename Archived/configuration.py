from configparser import ConfigParser

config = ConfigParser()
def load_config(arl_cookie):
    config['deezer'] = {
        'cookie_arl': arl_cookie
    }
    return config