<?php
/**
 * Applies a meta description override at render time.
 *
 * Each SEO plugin prints the description through its own filter; the override
 * is hooked into all of them at a late priority so it wins. A site with no SEO
 * plugin has no description tag at all, so for it the tag is printed here.
 * Pages without an override are left untouched.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Description filter registration.
 */
class HoshyarSEO_Description_Filters {

	const PRIORITY = 999;

	/**
	 * Override storage.
	 *
	 * @var HoshyarSEO_Description_Store
	 */
	private $store;

	/**
	 * Constructor.
	 *
	 * @param HoshyarSEO_Description_Store $store Override storage.
	 */
	public function __construct( HoshyarSEO_Description_Store $store ) {
		$this->store = $store;
	}

	/**
	 * Hook into every supported SEO plugin, and into the head for sites without one.
	 *
	 * @return void
	 */
	public function register() {
		add_filter( 'wpseo_metadesc', array( $this, 'plugin_description' ), self::PRIORITY );
		add_filter( 'rank_math/frontend/description', array( $this, 'plugin_description' ), self::PRIORITY );
		add_filter( 'aioseo_description', array( $this, 'plugin_description' ), self::PRIORITY );
		add_filter( 'seopress_titles_desc', array( $this, 'plugin_description' ), self::PRIORITY );
		add_action( 'wp_head', array( $this, 'core_description' ), 1 );
	}

	/**
	 * SEO plugin description, escaped for an attribute here: not every SEO plugin
	 * escapes after its filter, and esc_attr() never encodes an entity twice.
	 *
	 * @param string $description Description so far.
	 * @return string
	 */
	public function plugin_description( $description ) {
		$override = $this->current_override();
		return null === $override ? $description : esc_attr( $override );
	}

	/**
	 * The tag itself, only where no SEO plugin prints one.
	 *
	 * @return void
	 */
	public function core_description() {
		if ( self::seo_plugin_active() ) {
			return;
		}
		$override = $this->current_override();
		if ( null !== $override ) {
			echo '<meta name="description" content="' . esc_attr( $override ) . '" />' . "\n";
		}
	}

	/**
	 * Whether an SEO plugin that prints its own description is active.
	 *
	 * @return bool
	 */
	public static function seo_plugin_active() {
		return defined( 'WPSEO_VERSION' ) || class_exists( 'RankMath' ) || defined( 'AIOSEO_VERSION' ) || self::seopress_prints_meta();
	}

	/**
	 * SEOPress prints titles and descriptions only with its Titles & Metas
	 * module on; with it off, nothing it filters reaches the page.
	 *
	 * @return bool
	 */
	public static function seopress_prints_meta() {
		if ( ! defined( 'SEOPRESS_VERSION' ) ) {
			return false;
		}
		if ( function_exists( 'seopress_get_toggle_option' ) ) {
			return '1' === (string) seopress_get_toggle_option( 'titles' );
		}
		return true; // An older SEOPress without the toggle helper: assume it prints them.
	}

	/**
	 * Override for the page being rendered, if it is a single post of any type.
	 *
	 * @return string|null
	 */
	private function current_override() {
		if ( is_admin() || ! is_singular() ) {
			return null;
		}
		$post_id = get_queried_object_id();
		return $post_id ? $this->store->get( $post_id ) : null;
	}
}
